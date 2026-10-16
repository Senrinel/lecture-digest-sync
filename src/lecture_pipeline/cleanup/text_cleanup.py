"""Gemini AI Advanced Cleanup Engine (P5).

Supports 3 cleanup modes:
1. 'text': Pure text formatting, punctuation, and zero-summarization cleanup.
2. 'audio': Multimodal audio-grounded correction (listens to audio to fix homophones and terms).
3. 'fusion': Dual-channel transcript fusion (compares two STT transcripts to eliminate errors).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from rich.console import Console

from lecture_pipeline.cleanup.base import (
    BaseCleanupEngine,
    CleanedChunk,
    CleanupResult,
)

console = Console(legacy_windows=False)

CLEANUP_MODEL_CANDIDATES = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
]

DEFAULT_PROMPT_TEMPLATES = {
    "text": "prompts/library/fabric_clean_text.md",
    "audio": "prompts/library/audio_grounded_cleanup.md",
    "fusion": "prompts/library/fusion_cleanup.md",
    "strict": "prompts/library/strict_verbatim.md",
}


class GeminiTextCleanupEngine(BaseCleanupEngine):
    """Advanced transcript cleanup engine leveraging Google Gemini API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        primary_model: str = "gemini-3.5-flash-lite",
        prompt_template_path: Optional[str | Path] = None,
        mode: str = "text",
        temperature: float = 0.1,
        chunk_chars: int = 1500,
        overlap_chars: int = 200,
        min_ratio: float = 0.80,
        max_ratio: float = 1.20,
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("未設定 GEMINI_API_KEY，請於 .env 填入有效的 Gemini API 金鑰。")

        # Set 300s timeout for robust network stability
        http_opts = types.HttpOptions(timeout=300000)
        self.client = genai.Client(api_key=self.api_key, http_options=http_opts)

        self.primary_model = primary_model
        self.mode = mode.lower().strip()
        if self.mode not in ["text", "audio", "fusion"]:
            self.mode = "text"

        if prompt_template_path:
            p = Path(prompt_template_path)
            if not p.is_file() and not p.is_absolute():
                if (Path("prompts/library") / prompt_template_path).is_file():
                    p = Path("prompts/library") / prompt_template_path
                elif (Path("prompts") / prompt_template_path).is_file():
                    p = Path("prompts") / prompt_template_path
            self.prompt_template_path = p
        else:
            default_tpl = DEFAULT_PROMPT_TEMPLATES.get(self.mode, DEFAULT_PROMPT_TEMPLATES["text"])
            self.prompt_template_path = Path(default_tpl)

        self.temperature = temperature
        self.chunk_chars = chunk_chars
        self.overlap_chars = overlap_chars
        self.min_ratio = min_ratio
        self.max_ratio = max_ratio

    @property
    def engine_id(self) -> str:
        return f"gemini_{self.mode}"

    def load_prompt_template(self) -> str:
        """Load prompt template from file or return standard fallback template."""
        if self.prompt_template_path.is_file():
            try:
                with open(self.prompt_template_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                console.print(f"[yellow]⚠️ 無法讀取 Prompt 範本 {self.prompt_template_path}: {e}，使用內建標準範本。[/yellow]")

        return (
            "# 角色與目標\n"
            "你是一位高階學術編輯，請將輸入的大學課堂語音逐字稿整理為結構清晰、標點完整、分段清楚的高品質課堂文稿。\n\n"
            "# 核心約束 (絕對不可違反)\n"
            "1. 【字數與內容守恆】：嚴禁濃縮、嚴禁摘要、嚴禁刪減講師所講的任何實質內容與口語細節。輸出字數應與輸入字數大致守恆（容許 80%~105% 之間）。\n"
            "2. 【忠於原話原意】：保留講師原本的用語與教學語氣。不得任意改寫為自己的話。\n"
            "3. 【台灣繁體與專有名詞】：使用標準台灣繁體中文，英文專有名詞與公式符號保留原文大小寫。\n"
            "4. 僅輸出整理後的 Markdown 內文，嚴禁開場白或說明語。"
        )

    def build_prompt(
        self,
        raw_text_chunk: str,
        secondary_text_chunk: Optional[str] = None,
        course: Optional[str] = None,
        glossary: Optional[List[str]] = None,
        is_retry: bool = False,
        previous_ratio: float = 0.0,
    ) -> str:
        """Build complete instruction prompt for Gemini."""
        base_template = self.load_prompt_template()

        context_parts = []
        if course:
            context_parts.append(f"【本堂課程主題】：{course}")
        if glossary:
            terms = ", ".join(glossary[:40])
            context_parts.append(f"【專用名詞庫】：{terms}")

        context_block = "\n".join(context_parts) if context_parts else ""

        retry_warning = ""
        if is_retry:
            retry_warning = (
                f"\n\n> ⚠️ 注意 (最高警戒)：你上一次整理的產出字數比僅有 {previous_ratio:.1%}，"
                f"嚴重違反了「不可摘要、字數守恆」的核心原則！\n"
                f"> 請務必完整還原原始逐字稿中的每一句話與細節，切勿跳過任何句子或擅自總結！\n"
            )

        if self.mode == "fusion" and secondary_text_chunk:
            content_section = (
                f"--- 【通道 A 逐字稿】 ---\n"
                f"{raw_text_chunk}\n"
                f"--- 【通道 B 逐字稿】 ---\n"
                f"{secondary_text_chunk}\n"
                f"--- 逐字稿結束 ---\n\n"
                f"請對比以上兩份逐字稿，截長補短並消除單邊幻覺與漏字，直接輸出最高品質繁體中文融合文稿："
            )
        else:
            content_section = (
                f"--- 以下為待整理的課堂語音原始逐字稿 ---\n"
                f"{raw_text_chunk}\n"
                f"--- 原始逐字稿結束 ---\n\n"
                f"請開始整理並直接輸出 Markdown 課堂文稿："
            )

        full_prompt = (
            f"{base_template}\n\n"
            f"{context_block}"
            f"{retry_warning}\n\n"
            f"{content_section}"
        )
        return full_prompt

    def split_into_chunks(self, text: str) -> List[str]:
        """Split text into manageable chunks at natural sentence boundaries."""
        text = text.strip()
        if len(text) <= self.chunk_chars:
            return [text]

        chunks: List[str] = []
        start = 0
        text_len = len(text)
        break_chars = ["\n\n", "\n", "。", "！", "？", "；", "，"]

        while start < text_len:
            if text_len - start <= self.chunk_chars:
                chunks.append(text[start:])
                break

            target_end = start + self.chunk_chars
            best_cut = -1
            for bc in break_chars:
                pos = text.rfind(bc, start + int(self.chunk_chars * 0.7), target_end)
                if pos != -1:
                    best_cut = pos + len(bc)
                    break

            if best_cut == -1:
                best_cut = target_end

            chunks.append(text[start:best_cut].strip())
            start = best_cut

        return [c for c in chunks if c]

    def _call_gemini_api(
        self,
        prompt: str,
        audio_path: Optional[Path | str] = None,
    ) -> tuple[str, str]:
        """Execute Gemini API call with model fallback and transient error handling."""
        models_to_try = [self.primary_model] + [
            m for m in CLEANUP_MODEL_CANDIDATES if m != self.primary_model
        ]

        gen_config = types.GenerateContentConfig(
            temperature=self.temperature,
        )

        uploaded_file = None
        if audio_path and Path(audio_path).is_file():
            try:
                uploaded_file = self.client.files.upload(file=str(audio_path))
            except Exception as e:
                console.print(f"[yellow]⚠️ 無法上傳音檔至 Gemini Files API: {e}，降級為純文字整理模式。[/yellow]")
                uploaded_file = None

        try:
            last_error = None
            for model_id in models_to_try:
                try:
                    contents: list[Any] = []
                    if uploaded_file:
                        contents.append(uploaded_file)
                    contents.append(prompt)

                    response = self.client.models.generate_content(
                        model=model_id,
                        contents=contents,
                        config=gen_config,
                    )
                    output_text = (response.text or "").strip()
                    return output_text, model_id
                except Exception as e:
                    last_error = e
                    err_msg = str(e)
                    transient_keywords = [
                        "503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED",
                        "RemoteProtocolError", "Server disconnected",
                        "timeout", "timed out", "ConnectionReset",
                    ]
                    if any(kw.lower() in err_msg.lower() for kw in transient_keywords):
                        console.print(
                            f"[yellow]⚠️ 整理模型 {model_id} 遇尖峰或連線中斷 ({err_msg[:50]}...)，等待 5 秒後切換或重試...[/yellow]"
                        )
                        time.sleep(5.0)
                        continue
                    else:
                        raise e

            raise RuntimeError(f"所有 Gemini 模型候選皆整理失敗，最後錯誤: {last_error}")

        finally:
            if uploaded_file:
                try:
                    self.client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass

    def clean_single_chunk(
        self,
        chunk_index: int,
        raw_chunk: str,
        secondary_chunk: Optional[str] = None,
        audio_path: Optional[Path | str] = None,
        course: Optional[str] = None,
        glossary: Optional[List[str]] = None,
    ) -> CleanedChunk:
        """Clean a single text chunk with strict length guardrail & auto-retry."""
        start_t = time.time()
        raw_char_count = len(raw_chunk)

        # 1. First attempt
        prompt = self.build_prompt(
            raw_chunk,
            secondary_text_chunk=secondary_chunk,
            course=course,
            glossary=glossary,
        )
        cleaned_text, model_used = self._call_gemini_api(prompt, audio_path=audio_path)
        cleaned_char_count = len(cleaned_text)
        retention_ratio = round(cleaned_char_count / max(1, raw_char_count), 4)

        retried = False
        validation_passed = self.min_ratio <= retention_ratio <= self.max_ratio

        # 2. Guardrail: If summarized / truncated (< min_ratio), auto-retry once with strict alert
        if retention_ratio < self.min_ratio:
            retried = True
            console.print(
                f"[yellow]⚠️ 片段 #{chunk_index} 整理後字數比為 {retention_ratio:.1%} "
                f"(低於閾值 {self.min_ratio:.1%})，觸發嚴格防摘要二次校正...[/yellow]"
            )
            retry_prompt = self.build_prompt(
                raw_chunk,
                secondary_text_chunk=secondary_chunk,
                course=course,
                glossary=glossary,
                is_retry=True,
                previous_ratio=retention_ratio,
            )
            retry_text, retry_model = self._call_gemini_api(retry_prompt, audio_path=audio_path)
            retry_char_count = len(retry_text)
            retry_ratio = round(retry_char_count / max(1, raw_char_count), 4)

            # Choose the one closer to original length
            if abs(retry_ratio - 1.0) < abs(retention_ratio - 1.0):
                cleaned_text = retry_text
                cleaned_char_count = retry_char_count
                retention_ratio = retry_ratio
                model_used = retry_model

            validation_passed = self.min_ratio <= retention_ratio <= self.max_ratio
            if validation_passed:
                console.print(
                    f"[green]✔ 片段 #{chunk_index} 二次校正成功！字數比提升至 {retention_ratio:.1%}[/green]"
                )
            else:
                console.print(
                    f"[yellow]⚠️ 片段 #{chunk_index} 二次校正後字數比仍為 {retention_ratio:.1%}，標記為審計例外。[/yellow]"
                )

        latency = round(time.time() - start_t, 2)

        return CleanedChunk(
            index=chunk_index,
            raw_text=raw_chunk,
            cleaned_text=cleaned_text,
            raw_char_count=raw_char_count,
            cleaned_char_count=cleaned_char_count,
            retention_ratio=retention_ratio,
            model_used=model_used,
            latency_sec=latency,
            retried=retried,
            validation_passed=validation_passed,
        )

    def clean(
        self,
        raw_text: str,
        secondary_text: Optional[str] = None,
        audio_chunks: Optional[List[Path | str]] = None,
        course: Optional[str] = None,
        date: Optional[str] = None,
        glossary: Optional[List[str]] = None,
    ) -> CleanupResult:
        """Execute complete text cleanup workflow across all chunks."""
        start_total = time.time()
        raw_text_clean = raw_text.strip()
        raw_total_chars = len(raw_text_clean)

        if not raw_text_clean:
            return CleanupResult(
                full_cleaned_text="",
                raw_char_count=0,
                cleaned_char_count=0,
                retention_ratio=1.0,
                model_used=self.primary_model,
                prompt_template=self.prompt_template_path.name,
                total_latency_sec=0.0,
                validation_passed=True,
                chunks=[],
            )

        text_chunks = self.split_into_chunks(raw_text_clean)
        secondary_chunks = self.split_into_chunks(secondary_text) if secondary_text else []

        cleaned_chunks: List[CleanedChunk] = []
        full_cleaned_parts: List[str] = []

        for idx, chunk in enumerate(text_chunks, start=1):
            if idx > 1:
                time.sleep(2.5)  # Conservative pause between text cleanup calls to prevent rate limits
            sec_chunk = secondary_chunks[idx - 1] if idx - 1 < len(secondary_chunks) else None
            audio_p = audio_chunks[idx - 1] if audio_chunks and idx - 1 < len(audio_chunks) else None

            cleaned_chunk = self.clean_single_chunk(
                chunk_index=idx,
                raw_chunk=chunk,
                secondary_chunk=sec_chunk,
                audio_path=audio_p,
                course=course,
                glossary=glossary,
            )
            cleaned_chunks.append(cleaned_chunk)
            full_cleaned_parts.append(cleaned_chunk.cleaned_text)

        full_cleaned_text = "\n\n".join(full_cleaned_parts)
        cleaned_total_chars = len(full_cleaned_text)
        overall_retention = round(cleaned_total_chars / max(1, raw_total_chars), 4)
        overall_passed = all(c.validation_passed for c in cleaned_chunks)
        total_latency = round(time.time() - start_total, 2)

        models_used = list(dict.fromkeys(c.model_used for c in cleaned_chunks))
        model_str = ", ".join(models_used) if models_used else self.primary_model

        return CleanupResult(
            full_cleaned_text=full_cleaned_text,
            raw_char_count=raw_total_chars,
            cleaned_char_count=cleaned_total_chars,
            retention_ratio=overall_retention,
            model_used=model_str,
            prompt_template=self.prompt_template_path.name,
            total_latency_sec=total_latency,
            validation_passed=overall_passed,
            chunks=cleaned_chunks,
        )
