"""Stage: AI Transcript Cleanup.

Cleans up raw ASR transcripts into formatted, punctuated, readable lecture notes
without summarizing, deleting content, or altering technical concepts.
Enforces the Content & Character Length Conservation Check (0.80 <= ratio <= 1.05).
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple
from google import genai
from google.genai import errors
from opencc import OpenCC
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class TranscriptCleaner:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-flash-latest",
        prompt_template_path: str | Path = "prompts/library/fabric_clean_text.md",
        chunk_chars: int = 8000,
        overlap_chars: int = 300,
        ratio_min: float = 0.80,
        ratio_max: float = 1.05,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model_name
        self.chunk_chars = chunk_chars
        self.overlap_chars = overlap_chars
        self.ratio_min = ratio_min
        self.ratio_max = ratio_max
        self.opencc = OpenCC("s2twp")

        # Load system prompt template
        tpl_path = Path(prompt_template_path)
        if tpl_path.exists():
            self.system_prompt = tpl_path.read_text(encoding="utf-8")
        else:
            self.system_prompt = (
                "你是專業學術編輯。請整理這段課堂語音逐字稿，補全標點符號與適當分段。"
                "【核心約束】嚴禁刪除任何實質內容、嚴禁摘要，務必保持字數與內容守恆。使用台灣繁體中文。"
            )

    def _split_text_into_chunks(self, text: str) -> List[Tuple[int, str]]:
        """Splits text into chunks of roughly chunk_chars with overlap."""
        if len(text) <= self.chunk_chars:
            return [(0, text)]

        chunks = []
        start = 0
        idx = 0
        while start < len(text):
            end = min(start + self.chunk_chars, len(text))
            chunk = text[start:end]
            chunks.append((idx, chunk))
            idx += 1
            if end >= len(text):
                break
            start = end - self.overlap_chars
        return chunks

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        retry=retry_if_exception_type((errors.ServerError, errors.APIError)),
    )
    def _call_gemini_cleanup(self, chunk_text: str, chunk_index: int, total_chunks: int) -> str:
        prompt = (
            f"{self.system_prompt}\n\n"
            f"--- 課堂逐字稿片段 [{chunk_index + 1}/{total_chunks}] ---\n"
            f"{chunk_text}\n"
            f"--- 片段結束 ---"
        )
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"temperature": 0.1},
        )
        return response.text or ""

    def clean_transcript(self, raw_text: str) -> str:
        """Cleans a complete raw transcript enforcing length conservation checks."""
        chunks = self._split_text_into_chunks(raw_text)
        cleaned_parts = []

        for idx, chunk_text in chunks:
            input_len = len(chunk_text.strip())
            cleaned_chunk = ""
            attempts = 0

            while attempts < 2:
                attempts += 1
                try:
                    out = self._call_gemini_cleanup(chunk_text, idx, len(chunks))
                    out_len = len(out.strip())
                    ratio = out_len / input_len if input_len > 0 else 1.0

                    if self.ratio_min <= ratio <= self.ratio_max:
                        cleaned_chunk = out.strip()
                        break
                    else:
                        # Out of bounds: retry once
                        continue
                except Exception:
                    pass

            if not cleaned_chunk:
                # If length check failed or API error after retries, fallback to raw text with warning flag
                cleaned_chunk = (
                    f"\n<!-- ⚠ 片段 {idx + 1} 觸發字數守恆安全機制，保留原始辨識文字 -->\n"
                    f"{chunk_text.strip()}\n"
                )

            cleaned_parts.append(cleaned_chunk)

        merged = "\n\n".join(cleaned_parts)
        return self.opencc.convert(merged)

    def process_file(self, raw_txt_path: str | Path, output_md_path: str | Path) -> Path:
        """Reads 01_raw.txt, processes it, and writes 02_clean.md."""
        inp = Path(raw_txt_path)
        outp = Path(output_md_path)
        raw_text = inp.read_text(encoding="utf-8")

        cleaned = self.clean_transcript(raw_text)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(cleaned, encoding="utf-8")
        return outp
