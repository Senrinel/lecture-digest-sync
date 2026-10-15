"""Gemini API connectivity and quota probe script.

Tests the availability and response of candidate Gemini models defined in config.yaml.
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

api_key = os.getenv("GEMINI_API_KEY", "").strip()

if not api_key or api_key == "請在此填入你的_GEMINI_API_KEY":
    print("❌ 尚未設定有效的 GEMINI_API_KEY！")
    print(f"請開啟檔案：{project_root / '.env'}")
    print("將 GEMINI_API_KEY=\"AIza...\" 替換為你的真實 Google Gemini API Key。")
    sys.exit(1)

from google import genai
from rich.console import Console
from rich.table import Table

console = Console()
client = genai.Client(api_key=api_key)

candidate_models = [
    ("gemini-3.8-flash", "主力文本 / 音訊理解 (1M context)"),
    ("gemini-3.7-flash", "備用文本模型"),
    ("gemini-3.5-flash", "備用文本模型"),
    ("gemini-3.5-transcribe", "專用 ASR 轉錄模型"),
    ("gemini-3.1-flash-lite", "輕量備用模型"),
    ("gemini-flash-latest", "最新動態 Flash 指標"),
]

console.print("\n[bold cyan]🔍 正在測試 Gemini API 連線與候選模型可用性...[/bold cyan]\n")

table = Table(title="Gemini 模型連線探測報告")
table.add_column("Model ID", style="bold yellow")
table.add_column("用途定位", style="dim")
table.add_column("狀態", justify="center")
table.add_column("回應延遲 (s)", justify="right")
table.add_column("詳細訊息 / 錯誤")

for model_id, purpose in candidate_models:
    start_time = time.time()
    try:
        # Send a minimal probe prompt
        response = client.models.generate_content(
            model=model_id,
            contents="ping",
        )
        latency = round(time.time() - start_time, 2)
        text_preview = (response.text or "").strip().replace("\n", " ")[:30]
        table.add_row(
            model_id,
            purpose,
            "[green]✅ 正常可用[/green]",
            f"{latency:.2f}",
            f"回應成功: '{text_preview}'",
        )
    except Exception as e:
        latency = round(time.time() - start_time, 2)
        err_msg = str(e).strip().replace("\n", " ")
        if "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            status = "[yellow]⚠️ 額度已滿 (429)[/yellow]"
        elif "NOT_FOUND" in err_msg or "404" in err_msg:
            status = "[red]❌ 模型不存在 (404)[/red]"
        elif "PERMISSION_DENIED" in err_msg or "403" in err_msg:
            status = "[red]⛔ 無權限 (403)[/red]"
        else:
            status = "[red]❌ 失敗[/red]"
        table.add_row(
            model_id,
            purpose,
            status,
            f"{latency:.2f}",
            err_msg[:60] + "..." if len(err_msg) > 60 else err_msg,
        )

console.print(table)
console.print("\n[dim]提示：每日實際 RPD 上限請至 https://aistudio.google.com/rate-limit 查閱。[/dim]\n")
