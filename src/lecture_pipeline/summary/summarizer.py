"""Gemini AI Structured Lecture Summarizer for Phase 4.

Transforms cleaned lecture text into a highly structured, exam-oriented Markdown note
specifically designed for Google NotebookLM source import and efficient review.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from rich.console import Console

from lecture_pipeline.summary.base import BaseSummarizer, SummaryResult

console = Console(legacy_windows=False)

SUMMARIZE_MODEL_CANDIDATES = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
]


class GeminiLectureSummarizer(BaseSummarizer):
    """Generates structured lecture review notes leveraging Gemini API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        primary_model: str = "gemini-3.5-flash-lite",
        template_path: str | Path = "prompts/library/fabric_summarize_lecture.md",
        thinking_budget: int = 1024,
        temperature: float = 0.2,
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("未設定 GEMINI_API_KEY，請於 .env 填入有效的 Gemini API 金鑰。")

        # Set 300s timeout for robust processing
        http_opts = types.HttpOptions(timeout=300000)
        self.client = genai.Client(api_key=self.api_key, http_options=http_opts)
        self.primary_model = primary_model
        p = Path(template_path)
        if not p.is_file() and not p.is_absolute():
            if (Path("prompts/library") / template_path).is_file():
                p = Path("prompts/library") / template_path
            elif (Path("prompts") / template_path).is_file():
                p = Path("prompts") / template_path
        self.template_path = p
        self.thinking_budget = thinking_budget
        self.temperature = temperature

    @property
    def summarizer_id(self) -> str:
        return "gemini_lecture_summarizer"

    def load_template(self, course: Optional[str] = None) -> str:
        """Load prompt template from file or auto-select based on course/meeting type."""
        actual_template = self.template_path
        if course:
            is_meeting = any(
                k in course.lower()
                for k in ["會議", "meeting", "組會", "座談", "討論會", "研討會", "工作會", "訪談", "口試"]
            )
            if is_meeting and self.template_path.name == "fabric_summarize_lecture.md":
                meeting_t = Path("prompts/library/meeting_summary.md")
                if meeting_t.is_file():
                    actual_template = meeting_t

        if actual_template.is_file():
            try:
                with open(actual_template, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                console.print(f"[yellow]⚠️ 無法讀取總結範本 {actual_template}: {e}，使用內建標準範本。[/yellow]")

        return (
            "# 角色與目標\n"
            "你是一位頂尖的大學課程助教與筆記架構師。請依據提供的課堂整理文稿，產出一份結構嚴謹、重點清晰、便於複習與匯入 NotebookLM 的結構化課程筆記。\n\n"
            "# 規範區塊\n"
            "1. 【課程主旨】：1~3 句話概括核心重點。\n"
            "2. 【章節大綱】：條列主要段落與進度。\n"
            "3. 【核心概念與定義】：重要概念的精確定義（中英對照）。\n"
            "4. 【方法流程與公式推導】：清晰整理演算法步驟、數學證明與公式推導。\n"
            "5. 【課堂案例與生活應用】：記錄講師舉出的代表性例題或生活故事。\n"
            "6. 【講師強調重點與考前注意點】：標註考試常考或容易混淆的關鍵。\n"
            "7. 【關鍵詞彙對照表】：整理專有名詞與縮寫。\n"
            "使用標準台灣繁體中文，直接輸出 Markdown 內容。"
        )

    def build_prompt(
        self,
        cleaned_text: str,
        course: Optional[str] = None,
        date: Optional[str] = None,
        glossary: Optional[List[str]] = None,
    ) -> str:
        """Build instruction prompt for Gemini summarizer."""
        base_template = self.load_template(course=course)

        context_lines = []
        if course and course != "Unsorted":
            context_lines.append(f"【課程主題】：{course}")
        if date:
            context_lines.append(f"【課堂日期】：{date}")
        if glossary:
            context_lines.append(f"【專業術語對照庫】：{', '.join(glossary[:200])}")

        context_block = "\n".join(context_lines)

        full_prompt = (
            f"{base_template}\n\n"
            f"{context_block}\n\n"
            f"--- 以下為整理後之完整課堂文稿 ---\n"
            f"{cleaned_text}\n"
            f"--- 課堂文稿結束 ---\n\n"
            f"請依據上述範本規範，產出結構完整、格式精美的 Markdown 課堂重點總結筆記："
        )
        return full_prompt

    def summarize(
        self,
        cleaned_text: str,
        course: Optional[str] = None,
        date: Optional[str] = None,
        glossary: Optional[List[str]] = None,
    ) -> SummaryResult:
        """Execute summarization and generate structured markdown note."""
        start_time = time.time()
        cleaned_text_stripped = cleaned_text.strip()
        input_chars = len(cleaned_text_stripped)

        if not cleaned_text_stripped:
            return SummaryResult(
                full_summary_md="# 課堂總結\n\n（無輸入內文）",
                input_char_count=0,
                summary_char_count=0,
                model_used=self.primary_model,
                prompt_template=str(self.template_path),
                thinking_budget=self.thinking_budget,
                total_latency_sec=0.0,
            )

        prompt = self.build_prompt(
            cleaned_text=cleaned_text_stripped,
            course=course,
            date=date,
            glossary=glossary,
        )

        models_to_try = [self.primary_model] + [
            m for m in SUMMARIZE_MODEL_CANDIDATES if m != self.primary_model
        ]

        summary_md = ""
        model_used = self.primary_model
        last_error = None

        for model_id in models_to_try:
            # Lite models don't support thinking budgets; omit thinking_config entirely
            if "lite" in model_id.lower() or not self.thinking_budget:
                gen_config = types.GenerateContentConfig(temperature=self.temperature)
            else:
                gen_config = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget),
                    temperature=self.temperature,
                )

            for attempt in range(3):
                try:
                    response = self.client.models.generate_content(
                        model=model_id,
                        contents=[prompt],
                        config=gen_config,
                    )
                    summary_md = (response.text or "").strip()
                    model_used = model_id
                    break
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
                        "INVALID_ARGUMENT",
                    ]
                    if any(kw.lower() in err_msg.lower() for kw in transient_keywords):
                        console.print(
                            f"[yellow]⚠️ 總結模型 {model_id} (嘗試 {attempt + 1}/3) 遇伺服器尖峰或連線中斷 ({err_msg[:60]}...)，保守等待 6 秒後重試...[/yellow]"
                        )
                        time.sleep(6.0)
                        continue
                    else:
                        raise e

            if summary_md:
                break
        else:
            raise RuntimeError(f"所有總結模型候選皆執行失敗，最後錯誤: {last_error}")

        total_latency = round(time.time() - start_time, 2)
        summary_chars = len(summary_md)

        return SummaryResult(
            full_summary_md=summary_md,
            input_char_count=input_chars,
            summary_char_count=summary_chars,
            model_used=model_used,
            prompt_template=str(self.template_path),
            thinking_budget=self.thinking_budget,
            total_latency_sec=total_latency,
            audit_metadata={
                "course": course,
                "date": date,
            },
        )
