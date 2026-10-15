"""Gemini AI Text Cleanup Engine for Phase 3.

Transforms raw ASR transcripts into formatted, punctuated, and paragraph-structured
Traditional Chinese lecture text while strictly preserving full content length (zero summarization).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from rich.console import Console

from lecture_pipeline.cleanup.base import (
    BaseCleanupEngine,
    CleanedChunk,
    CleanupResult,
)

console = Console()

CLEANUP_MODEL_CANDIDATES = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
]


class GeminiTextCleanupEngine(BaseCleanupEngine):
    """Text-based transcript cleanup engine leveraging Google Gemini API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        primary_model: str = "gemini-3.5-flash",
        prompt_template_path: str | Path = "prompts/library/fabric_clean_text.md",
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
        self.prompt_template_path = Path(prompt_template_path)
        self.temperature = temperature
        self.chunk_chars = chunk_chars
        self.overlap_chars = overlap_chars
        self.min_ratio = min_ratio
        self.max_ratio = max_ratio

    @property
    def engine_id(self) -> str:
        return "gemini_text"

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
        course: Optional[str] = None,
        glossary: Optional[List[str]] = None,
        is_retry: bool = False,
        previous_ratio: float = 0.0,
    ) -> str:
        """Build complete instruction prompt for Gemini."""
        base_template = self.load_prompt_template()

        context_lines = []
        if course and course != "Unsorted":
            context_lines.append(f"\n【本堂課程主題】：{course}")

        if glossary:
            context_lines.append(
                f"【專業術語參考庫 (請優先校正為正確詞彙)】：{', '.join(glossary[:200])}"
            )

        if is_retry:
            context_lines.append(
                f"\n🚨【CRITICAL WARNING - 嚴重警告】\n"
                f"您前一次輸出的字數僅有輸入字數的 {previous_ratio:.1%}，被判定為「過度精簡/摘要/漏字」！\n"
                f"本系統是供課堂複習使用的【完整全文文稿】，嚴格禁止任何形式的摘要、刪減或大意總結！\n"
                f"請務必逐句完整還原所有細節與口語論述，僅修復口誤錯字、補全標點與合理分段，輸出長度必須維持在 80%~105% 之間！"
            )

        context_block = "\n".join(context_lines) if context_lines else ""

        full_prompt = (
            f"{base_template}\n\n"
            f"{context_block}\n\n"
            f"--- 以下為待整理的課堂語音原始逐字稿 ---\n"
            f"{raw_text_chunk}\n"
            f"--- 原始逐字稿結束 ---\n\n"
            f"請開始整理並直接輸出 Markdown 課堂文稿："
        )
        return full_prompt

    def split_into_chunks(self, text: str) -> List[str]:
        """Split text into manageable chunks at natural sentence boundaries if needed."""
        text = text.strip()
        if len(text) <= self.chunk_chars:
            return [text]

        chunks: List[str] = []
        start = 0
        text_len = len(text)

        # Delimiters indicating natural breaks in Chinese lecture speech
        break_chars = ["\n\n", "\n", "。", "！", "？", "；", "，"]

        while start < text_len:
            if text_len - start <= self.chunk_chars:
                chunks.append(text[start:])
                break

            target_end = start + self.chunk_chars
            # Look backwards from target_end for a break character
            best_cut = -1
            for bc in break_chars:
                pos = text.rfind(bc, start + int(self.chunk_chars * 0.7), target_end)
                if pos != -1:
                    best_cut = pos + len(bc)
                    break

            if best_cut == -1:
                # No clean punctuation found, cut at target_end
                best_cut = target_end

            chunks.append(text[start:best_cut].strip())
            start = best_cut

        return [c for c in chunks if c]

    def _call_gemini_api(self, prompt: str) -> tuple[str, str]:
        """Execute Gemini API call with model fallback and transient error handling."""
        models_to_try = [self.primary_model] + [
            m for m in CLEANUP_MODEL_CANDIDATES if m != self.primary_model
        ]

        gen_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            temperature=self.temperature,
        )

        last_error = None
        for model_id in models_to_try:
            try:
                response = self.client.models.generate_content(
                    model=model_id,
                    contents=[prompt],
                    config=gen_config,
                )
                output_text = (response.text or "").strip()
                return output_text, model_id
            except Exception as e:
                last_error = e
                err_msg = str(e)
                transient_keywords = [
                    "503",
                    "429",
                    "UNAVAILABLE",
                    "RESOURCE_EXHAUSTED",
                    "RemoteProtocolError",
                    "Server disconnected",
                    "timeout",
                    "timed out",
                    "ConnectionReset",
                ]
                if any(kw.lower() in err_msg.lower() for kw in transient_keywords):
                    console.print(
                        f"[yellow]⚠️ 整理模型 {model_id} 遇尖峰或連線中斷 ({err_msg[:50]}...)，正在自動切換至備援模型...[/yellow]"
                    )
                    time.sleep(2.0)
                    continue
                else:
                    raise e

        raise RuntimeError(f"所有 Gemini 模型候選皆整理失敗，最後錯誤: {last_error}")

    def clean_single_chunk(
        self,
        chunk_index: int,
        raw_chunk: str,
        course: Optional[str] = None,
        glossary: Optional[List[str]] = None,
    ) -> CleanedChunk:
        """Clean a single text chunk with strict length guardrail & auto-retry."""
        start_t = time.time()
        raw_char_count = len(raw_chunk)

        # 1. First attempt
        prompt = self.build_prompt(raw_chunk, course=course, glossary=glossary)
        cleaned_text, model_used = self._call_gemini_api(prompt)
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
                course=course,
                glossary=glossary,
                is_retry=True,
                previous_ratio=retention_ratio,
            )
            retry_text, retry_model = self._call_gemini_api(retry_prompt)
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
                prompt_template=str(self.prompt_template_path),
                validation_passed=True,
                total_latency_sec=0.0,
            )

        # 1. Segment raw text if necessary
        text_chunks = self.split_into_chunks(raw_text_clean)
        cleaned_chunks: List[CleanedChunk] = []

        for idx, chunk in enumerate(text_chunks, start=1):
            c_result = self.clean_single_chunk(
                chunk_index=idx,
                raw_chunk=chunk,
                course=course,
                glossary=glossary,
            )
            cleaned_chunks.append(c_result)

        # 2. Combine cleaned chunks
        full_cleaned = "\n\n".join(c.cleaned_text for c in cleaned_chunks)
        cleaned_total_chars = len(full_cleaned)
        overall_retention = round(cleaned_total_chars / max(1, raw_total_chars), 4)
        overall_validation = all(c.validation_passed for c in cleaned_chunks)

        total_latency = round(time.time() - start_total, 2)
        primary_model_used = cleaned_chunks[0].model_used if cleaned_chunks else self.primary_model

        return CleanupResult(
            full_cleaned_text=full_cleaned,
            raw_char_count=raw_total_chars,
            cleaned_char_count=cleaned_total_chars,
            retention_ratio=overall_retention,
            model_used=primary_model_used,
            prompt_template=str(self.prompt_template_path),
            min_ratio=self.min_ratio,
            max_ratio=self.max_ratio,
            validation_passed=overall_validation,
            total_latency_sec=total_latency,
            chunks=cleaned_chunks,
            audit_metadata={
                "course": course,
                "date": date,
                "chunks_count": len(cleaned_chunks),
                "any_retried": any(c.retried for c in cleaned_chunks),
            },
        )
