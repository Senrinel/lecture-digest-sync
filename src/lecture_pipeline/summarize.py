"""Stage: AI Lecture Key-point Summarization.

Generates structured study notes from cleaned lecture transcripts using high-star
prompt templates (e.g., Fabric summarize_lecture / extract_wisdom).
Outputs Markdown formatted 03_notes.md.
"""

import os
from pathlib import Path
from typing import Optional
from google import genai
from google.genai import errors
from opencc import OpenCC
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class LectureSummarizer:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-flash-latest",
        template_path: str | Path = "prompts/library/fabric_summarize_lecture.md",
        thinking_budget: Optional[int] = 4096,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model_name
        self.thinking_budget = thinking_budget
        self.opencc = OpenCC("s2twp")

        tpl_path = Path(template_path)
        if tpl_path.exists():
            self.system_prompt = tpl_path.read_text(encoding="utf-8")
        else:
            self.system_prompt = (
                "你是頂尖大學助教。請針對整份課堂文稿，產出結構完整、重點清晰的重點筆記。"
                "章節必須包含：課程主旨、章節大綱、核心定義、推導步驟、例題案例、考試考點與名詞對照表。"
            )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        retry=retry_if_exception_type((errors.ServerError, errors.APIError)),
    )
    def _call_gemini_summary(self, full_text: str, course_name: str, date_str: str) -> str:
        prompt = (
            f"{self.system_prompt}\n\n"
            f"=== 課程基本資訊 ===\n"
            f"課程：{course_name}\n"
            f"日期：{date_str}\n\n"
            f"=== 完整課堂文稿 ===\n"
            f"{full_text}\n"
            f"=== 文稿結束 ==="
        )

        config_dict = {"temperature": 0.2}
        if self.thinking_budget:
            config_dict["thinking_config"] = {"thinking_budget": self.thinking_budget}

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config_dict,
        )
        return response.text or ""

    def summarize_transcript(self, text: str, course_name: str = "", date_str: str = "") -> str:
        """Generates structured notes from lecture text."""
        raw_notes = self._call_gemini_summary(text, course_name, date_str)
        return self.opencc.convert(raw_notes.strip())

    def process_file(
        self,
        clean_md_path: str | Path,
        output_notes_path: str | Path,
        course_name: str = "",
        date_str: str = "",
    ) -> Path:
        """Reads 02_clean.md, generates summary notes, and writes 03_notes.md."""
        inp = Path(clean_md_path)
        outp = Path(output_notes_path)

        text = inp.read_text(encoding="utf-8")
        notes = self.summarize_transcript(text, course_name=course_name, date_str=date_str)

        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(notes, encoding="utf-8")
        return outp
